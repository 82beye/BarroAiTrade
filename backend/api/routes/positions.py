"""
포지션 및 계좌 API 라우터

엔드포인트:
  GET /api/accounts/balance       - 잔고 조회
  GET /api/positions              - 포지션 목록 조회
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import SecretStr

from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


_kiwoom_oauth = None

def _build_kiwoom_fetcher():
    global _kiwoom_oauth
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
    if _kiwoom_oauth is None:
        _kiwoom_oauth = KiwoomNativeOAuth(
            app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
            app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
    return KiwoomNativeAccountFetcher(oauth=_kiwoom_oauth)


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
        fetcher = _build_kiwoom_fetcher()
        balance = await fetcher.fetch_balance()
        holdings = balance.holdings or []
        if symbol:
            holdings = [h for h in holdings if h.symbol == symbol]
        positions = [
            {
                "symbol": h.symbol,
                "name": getattr(h, "name", ""),
                "quantity": int(h.qty),
                "avg_price": float(getattr(h, "avg_buy_price", 0)),
                "cur_price": float(getattr(h, "cur_price", 0)),
                "pnl_rate": float(getattr(h, "pnl_rate", 0)),
            }
            for h in holdings
        ]
        logger.info("포지션 목록 조회: %d 종목", len(positions))
        return {"positions": positions, "count": len(positions), "status": "ok"}
    except Exception as e:
        logger.error(f"포지션 조회 실패: {e}")
        return {"positions": [], "count": 0, "status": "error", "detail": str(e)}
