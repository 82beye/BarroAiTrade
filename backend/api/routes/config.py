"""
설정 API 라우터

엔드포인트:
  GET /api/config                - 현재 설정 조회
  PUT /api/config                - 설정 저장
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from fastapi import APIRouter, Body, HTTPException

from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config")
async def get_config() -> dict:
    """
    현재 설정 조회

    응답:
    ```json
    {
      "trading_mode": "simulation",
      "market": "stock",
      "scan_interval_sec": 3,
      "kiwoom": {
        "mock": true,
        "base_url": "https://openapi.koreainvestment.com:9443"
      }
    }
    ```
    """
    try:
        config = app_state.trading_config or {}
        logger.info("설정 조회")

        return {
            "trading_mode": config.get("mode", "simulation"),
            "market": config.get("market", "stock"),
            "scan_interval_sec": config.get("scan_interval_sec", 3),
            "kiwoom": config.get("kiwoom", {}),
        }
    except Exception as e:
        logger.error(f"설정 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_config(
    config_data: Dict[str, Any] = Body(...),
) -> dict:
    """
    설정 저장

    요청 본문:
    ```json
    {
      "trading_mode": "simulation",
      "market": "stock",
      "scan_interval_sec": 5
    }
    ```

    응답:
    ```json
    {
      "success": true,
      "message": "설정이 저장되었습니다",
      "config": {...}
    }
    ```
    """
    try:
        # 설정 유효성 검사
        if "trading_mode" in config_data:
            if config_data["trading_mode"] not in ["simulation", "live"]:
                raise ValueError("trading_mode은 simulation 또는 live여야 합니다")

        if "market" in config_data:
            if config_data["market"] not in ["stock", "crypto"]:
                raise ValueError("market은 stock 또는 crypto여야 합니다")

        # 설정 저장
        app_state.trading_config = config_data
        logger.info(f"설정 저장 완료: {list(config_data.keys())}")

        return {
            "success": True,
            "message": "설정이 저장되었습니다",
            "config": config_data,
        }
    except ValueError as e:
        logger.error(f"설정 검증 오류: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"설정 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
