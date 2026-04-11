"""
감시 종목 API 라우터

엔드포인트:
  GET /api/watchlist           - 감시 종목 목록 조회
  POST /api/watchlist          - 종목 추가
  DELETE /api/watchlist/:symbol - 종목 제거
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Path, Body, HTTPException

from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/watchlist")
async def get_watchlist() -> dict:
    """
    감시 종목 목록 조회

    응답:
    ```json
    {
      "symbols": ["005930", "000660", "051910"],
      "count": 3
    }
    ```
    """
    try:
        watchlist = app_state.watchlist or []
        logger.info(f"감시 종목 조회: {len(watchlist)} 종목")

        return {
            "symbols": watchlist,
            "count": len(watchlist),
        }
    except Exception as e:
        logger.error(f"감시 종목 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist")
async def add_watchlist(
    symbol: str = Body(..., embed=True),
) -> dict:
    """
    감시 종목 추가

    요청 본문:
    ```json
    {
      "symbol": "005930"
    }
    ```

    응답:
    ```json
    {
      "symbol": "005930",
      "added": true,
      "count": 4
    }
    ```
    """
    try:
        if not app_state.watchlist:
            app_state.watchlist = []

        symbol = symbol.upper().strip()

        if symbol not in app_state.watchlist:
            app_state.watchlist.append(symbol)
            logger.info(f"감시 종목 추가: {symbol}")

        return {
            "symbol": symbol,
            "added": True,
            "count": len(app_state.watchlist),
        }
    except Exception as e:
        logger.error(f"감시 종목 추가 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{symbol}")
async def remove_watchlist(
    symbol: str = Path(..., description="종목 코드"),
) -> dict:
    """
    감시 종목 제거

    응답:
    ```json
    {
      "symbol": "005930",
      "removed": true,
      "count": 2
    }
    ```
    """
    try:
        if not app_state.watchlist:
            app_state.watchlist = []

        symbol = symbol.upper().strip()

        if symbol in app_state.watchlist:
            app_state.watchlist.remove(symbol)
            logger.info(f"감시 종목 제거: {symbol}")

        return {
            "symbol": symbol,
            "removed": True,
            "count": len(app_state.watchlist),
        }
    except Exception as e:
        logger.error(f"감시 종목 제거 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
