"""
ComplianceService — 감사 로그 및 규정 준수 체크
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ComplianceService:
    """모든 주문 이벤트를 DB에 기록하고 이상 패턴을 감지"""

    def __init__(self, db_session=None) -> None:
        self._db = db_session

    async def log_event(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        market_type: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        pnl: Optional[float] = None,
        strategy_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """감사 이벤트 기록

        event_type 예시:
            order_placed, order_filled, risk_blocked, force_close,
            trading_started, trading_stopped, config_changed
        """
        entry = {
            "event_type": event_type,
            "symbol": symbol,
            "market_type": market_type,
            "quantity": quantity,
            "price": price,
            "pnl": pnl,
            "strategy_id": strategy_id,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }
        logger.info("[AUDIT] %s", entry)
        # TODO: DB 저장 구현 (Phase 1 백엔드 엔지니어)

    async def check_anomaly(self, symbol: str, trade_count: int) -> Optional[str]:
        """이상 패턴 감지 — 동일 종목 과매매 등"""
        if trade_count > 10:
            return f"{symbol} 과매매 감지: {trade_count}회"
        return None
