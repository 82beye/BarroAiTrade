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

from datetime import time as _dtime

from backend.core.scanner import SignalScanner
from backend.core.strategy.f_zone import FZoneParams
from backend.core.strategy.blue_line import BlueLineParams
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
        # BAR-OPS-09 Phase 2/3: 변동성 필터 운영 경로 적용 — ATR% < 3.5% 차단.
        # BAR-OPS-09 Phase 8e/8f: 진입 시간 게이트 — 14:00 이후 운영 신규 진입 차단.
        # 2026-05-29: gold_zone 1m+0.035 일관화(제안1) 원복 — 대규모 격자 백테스트에서
        #   1분봉+0.035 는 gold 신호 거의 전멸(실증)이라 근거 없음. gold 는 default 유지.
        #   (참고: 자동매수 데몬은 일봉 선정으로 작동 — docs/04-report/features/2026-05-29-grid-backtest.md)
        scanner = SignalScanner(
            gateway,
            f_zone_params=FZoneParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
            blue_line_params=BlueLineParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
        )
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
    최근 정제된 신호 조회 (데몬이 시뮬+국면 필터 후 저장한 시그널)

    응답:
    ```json
    {
      "signals": [...],
      "regime": "sideways",
      "timestamp": "2026-05-18T10:00:00+09:00"
    }
    ```
    """
    import json
    from datetime import datetime, timezone, timedelta, time as dtime
    from pathlib import Path

    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    market_open = dtime(8, 58)
    market_close = dtime(15, 30)

    # 장 외 시간: 빈 시그널 반환
    if now_kst.weekday() >= 5:
        return {"signals": [], "timestamp": now_kst.isoformat(), "status": "closed"}
    if not (market_open <= now_kst.time() <= market_close):
        return {"signals": [], "timestamp": now_kst.isoformat(), "status": "closed"}

    # 데몬이 저장한 정제된 시그널 파일 읽기
    refined_path = Path(__file__).resolve().parents[3] / "data" / "refined_signals.json"
    if refined_path.exists():
        try:
            data = json.loads(refined_path.read_text(encoding="utf-8"))
            signals = data.get("signals", [])[:limit]
            return {
                "signals": signals,
                "regime": data.get("regime", "unknown"),
                "timestamp": data.get("timestamp"),
                "status": "ok",
            }
        except Exception as e:
            logger.warning("refined_signals.json 읽기 실패: %s", e)

    return {"signals": [], "timestamp": now_kst.isoformat(), "status": "no_data"}
