"""
리포트 API 라우터

엔드포인트:
  GET /api/reports/daily            - 일일 리포트
  GET /api/reports/performance      - 성과 리포트
"""
from __future__ import annotations

import logging
from datetime import datetime, date

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/reports/daily")
async def get_daily_report(
    date_str: str = Query(None, description="조회 날짜 (YYYY-MM-DD)"),
) -> dict:
    """
    일일 리포트 조회

    응답:
    ```json
    {
      "date": "2026-04-11",
      "summary": {
        "trades_count": 5,
        "win_count": 3,
        "loss_count": 2,
        "win_rate": 60.0,
        "pnl": 50000,
        "pnl_pct": 0.5
      },
      "trades": [
        {
          "symbol": "005930",
          "side": "buy",
          "entry_price": 75000,
          "exit_price": 75500,
          "pnl": 5000,
          "entry_time": "2026-04-11T10:00:00Z",
          "exit_time": "2026-04-11T11:00:00Z"
        }
      ]
    }
    ```
    """
    try:
        # date_str 파싱
        if date_str:
            try:
                report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="날짜 형식: YYYY-MM-DD"
                )
        else:
            report_date = date.today()

        logger.info(f"일일 리포트 조회: {report_date}")

        # TODO: 거래 데이터베이스에서 실제 데이터 조회
        # 현재는 모의 데이터 반환
        return {
            "date": report_date.isoformat(),
            "summary": {
                "trades_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "pnl": 0,
                "pnl_pct": 0.0,
            },
            "trades": [],
            "status": "mock",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"일일 리포트 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/performance")
async def get_performance_report(
    period: str = Query("1m", description="기간: 1w, 1m, 3m, ytd, all"),
) -> dict:
    """
    성과 리포트 조회

    응답:
    ```json
    {
      "period": "1m",
      "summary": {
        "total_return": 5.5,
        "win_rate": 60.0,
        "profit_factor": 1.5,
        "max_drawdown": 2.5,
        "sharpe_ratio": 1.2,
        "trades_count": 100
      },
      "monthly": [
        {
          "month": "2026-03",
          "return": 3.2,
          "trades": 50
        }
      ]
    }
    ```
    """
    try:
        logger.info(f"성과 리포트 조회: {period}")

        # TODO: 성과 계산 로직 구현
        # 현재는 모의 데이터 반환
        return {
            "period": period,
            "summary": {
                "total_return": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "trades_count": 0,
            },
            "monthly": [],
            "status": "mock",
        }
    except Exception as e:
        logger.error(f"성과 리포트 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
