"""
AuditRepository — 감사 로그 DB 저장소

ComplianceService가 이 레포지터리를 통해 감사 이벤트를 영속화.
aiosqlite 미설치 시 no-op으로 동작.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.db.database import get_db

logger = logging.getLogger(__name__)


class AuditRepository:
    """감사 로그 CRUD"""

    async def insert(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        market_type: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        pnl: Optional[float] = None,
        strategy_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> bool:
        """감사 이벤트 삽입. 실패 시 False 반환 (시스템 중단 없음)."""
        try:
            async with get_db() as db:
                if db is None:
                    return False
                await db.execute(
                    """
                    INSERT INTO audit_log
                        (event_type, symbol, market_type, quantity, price, pnl,
                         strategy_id, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        symbol,
                        market_type,
                        quantity,
                        price,
                        pnl,
                        strategy_id,
                        json.dumps(metadata or {}),
                        created_at,
                    ),
                )
                await db.commit()
            return True
        except Exception as e:
            logger.error("감사 로그 DB 저장 실패: %s", e)
            return False

    async def find_recent(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """최근 감사 이벤트 조회"""
        try:
            async with get_db() as db:
                if db is None:
                    return []
                if event_type:
                    cursor = await db.execute(
                        "SELECT * FROM audit_log WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                        (event_type, limit),
                    )
                else:
                    cursor = await db.execute(
                        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error("감사 로그 조회 실패: %s", e)
            return []


audit_repo = AuditRepository()
