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
from backend.core.gateway.base import MarketGateway

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 게이트웨이 주입 (현재 mock) ─────────────────────────────────────────────────
# TODO: 실제 마켓 게이트웨이(키움, Binance 등) 연동 시 여기서 초기화
scanner: Optional[SignalScanner] = None


async def _get_scanner(market_type: MarketType = MarketType.STOCK) -> SignalScanner:
    """스캐너 인스턴스 반환 (lazy initialization)"""
    global scanner
    if scanner is None:
        # TODO: gateway 주입
        # gateway = get_market_gateway(market_type)
        # scanner = SignalScanner(gateway)
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화 - 설정 필요"
        )
    return scanner


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

    try:
        mt = MarketType.CRYPTO if market_type.lower() == "crypto" else MarketType.STOCK
        # scanner_instance = await _get_scanner(mt)
        # signals = await scanner_instance.scan(symbol_list)

        # TODO: 게이트웨이 연동 후 주석 제거
        logger.warning("신호 스캐닝 API: 게이트웨이 미초기화 - 임시 응답 반환")

        return {
            "market_type": market_type,
            "scanned_count": len(symbol_list),
            "signal_count": 0,
            "signals": [],
            "status": "not_ready",
            "message": "마켓 게이트웨이 초기화 대기 중"
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

    # TODO: Redis/메모리 캐시에서 최근 신호 조회
    return {
        "signals": [],
        "timestamp": None,
        "status": "not_ready"
    }
