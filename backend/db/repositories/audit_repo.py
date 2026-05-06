"""
AuditRepository — 감사 로그 DB 저장소.

ComplianceService 가 본 레포지터리를 통해 감사 이벤트를 영속화.
SQLAlchemy `text()` + named param 으로 dialect 무관 (SQLite fallback / Postgres 양립).
BAR-56 변경: `?` → `:name`, `await db.commit()` 제거 (get_db 가 트랜잭션 begin/commit 보장).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.db.database import get_db

logger = logging.getLogger(__name__)


class AuditRepository:
    """감사 로그 CRUD."""

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
                # SQLite metadata 컬럼은 TEXT — JSON 문자열로 저장.
                # Postgres metadata 컬럼은 JSONB (0001_init.py) — dict 직접 bind.
                metadata_payload: Any = metadata or {}
                if db.engine.dialect.name == "sqlite":
                    metadata_payload = json.dumps(metadata_payload, ensure_ascii=False)

                await db.execute(
                    text(
                        """
                        INSERT INTO audit_log
                            (event_type, symbol, market_type, quantity, price, pnl,
                             strategy_id, metadata, created_at)
                        VALUES
                            (:event_type, :symbol, :market_type, :quantity, :price, :pnl,
                             :strategy_id, :metadata, :created_at)
                        """
                    ),
                    {
                        "event_type": event_type,
                        "symbol": symbol,
                        "market_type": market_type,
                        "quantity": quantity,
                        "price": price,
                        "pnl": pnl,
                        "strategy_id": strategy_id,
                        "metadata": metadata_payload,
                        "created_at": created_at,
                    },
                )
            return True
        except Exception as e:
            logger.error("감사 로그 DB 저장 실패: %s", e)
            return False

    async def find_recent(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """최근 감사 이벤트 조회."""
        try:
            async with get_db() as db:
                if db is None:
                    return []
                if event_type:
                    sql = text(
                        "SELECT * FROM audit_log WHERE event_type = :event_type "
                        "ORDER BY created_at DESC LIMIT :limit"
                    )
                    params = {"event_type": event_type, "limit": limit}
                else:
                    sql = text(
                        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT :limit"
                    )
                    params = {"limit": limit}

                result = await db.execute(sql, params)
                return [dict(row) for row in result.mappings().all()]
        except Exception as e:
            logger.error("감사 로그 조회 실패: %s", e)
            return []


audit_repo = AuditRepository()
