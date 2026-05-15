"""
신호 스캐닝 API 라우터

엔드포인트:
  GET  /api/signals/scan?symbols=005930,035720  - 종목 스캔
  GET  /api/signals/recent  - 최근 신호 조회
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException

from backend.core.scanner import SignalScanner
from backend.models.signal import EntrySignal
from backend.models.market import MarketType
from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/signals/scan")
async def scan_signals(
    symbols: str = Query(..., description="쉼표로 구분된 종목 코드 (예: 005930,035720)"),
    market_type: str = Query("stock", description="시장 유형: stock, crypto"),
) -> dict:
    """
    종목 리스트를 스캔하여 매매 신호 추출

    응답:
    ```json
    {
      "market_type": "stock",
      "scanned_count": 2,
      "signal_count": 1,
      "signals": [
        {
          "symbol": "005930",
          "name": "삼성전자",
          "price": 75000,
          "signal_type": "f_zone",
          "score": 8.5,
          "reason": "...",
          "timestamp": "2026-04-11T10:00:00Z"
        }
      ]
    }
    ```
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    gateway = app_state.market_gateway
    if gateway is None:
        logger.warning("신호 스캐닝 API: 게이트웨이 미초기화 - 임시 응답 반환")
        return {
            "market_type": market_type,
            "scanned_count": len(symbol_list),
            "signal_count": 0,
            "signals": [],
            "status": "not_ready",
            "message": "마켓 게이트웨이 초기화 대기 중",
        }

    try:
        scanner = SignalScanner(gateway)
        signals = await scanner.scan(symbol_list)
        return {
            "market_type": market_type,
            "scanned_count": len(symbol_list),
            "signal_count": len(signals),
            "signals": [s.model_dump(mode="json") for s in signals],
            "status": "ok",
        }
    except Exception as e:
        logger.error("신호 스캔 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/recent")
async def get_recent_signals(
    limit: int = Query(10, ge=1, le=100, description="최대 신호 수"),
) -> dict:
    """
    최근 신호 조회 (메모리 캐시에서)

    응답:
    ```json
    {
      "signals": [...],
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    logger.info("최근 신호 %d개 조회", limit)

    import os
    from datetime import datetime, timezone, timedelta
    from pydantic import SecretStr
    try:
        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
        from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker

        # 모듈 수준 싱글톤 — 토큰 캐시 유지
        if not hasattr(get_recent_signals, "_oauth"):
            get_recent_signals._oauth = KiwoomNativeOAuth(
                app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
                app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
                base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
            )
        oauth = get_recent_signals._oauth
        # 60초 캐시 — rank API rate limit 방어
        import time
        cache = getattr(get_recent_signals, "_cache", None)
        if cache and time.time() - cache["ts"] < 60:
            return cache["data"]

        picker = KiwoomNativeLeaderPicker(oauth=oauth, min_score=0.5)
        leaders = await picker.pick(top_n=limit)
        KST = timezone(timedelta(hours=9))
        now_iso = datetime.now(KST).isoformat()
        signals = [
            {
                "symbol": l.symbol,
                "name": getattr(l, "name", ""),
                "score": round(float(getattr(l, "score", 0)), 3),
                "direction": "BUY",
                "flu_rate": float(getattr(l, "flu_rate", 0)),
                "cur_price": float(getattr(l, "cur_price", 0)),
                "ts": now_iso,
            }
            for l in leaders
        ]
        result = {
            "signals": signals,
            "timestamp": now_iso,
            "status": "ok",
        }
        get_recent_signals._cache = {"ts": time.time(), "data": result}
        return result
    except Exception as e:
        logger.warning("시그널 조회 실패: %s", e)
        # 캐시된 데이터 있으면 반환
        cache = getattr(get_recent_signals, "_cache", None)
        if cache:
            return cache["data"]
        return {"signals": [], "timestamp": None, "status": "error", "detail": str(e)}
