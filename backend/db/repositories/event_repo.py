"""BAR-61 — EventRepository.

text() + named param + dialect 분기 (BAR-56/57/58/59 패턴 답습).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.db.database import get_db
from backend.models.event import MarketEvent

logger = logging.getLogger(__name__)


class EventRepository:
    """market_events CRUD."""

    async def insert(self, event: MarketEvent) -> Optional[int]:
        """UNIQUE(symbol, event_date, event_type) 중복 시 None."""
        try:
            async with get_db() as db:
                if db is None:
                    return None
                is_sqlite = db.engine.dialect.name == "sqlite"
                if is_sqlite:
                    metadata_payload: Any = json.dumps(event.metadata, ensure_ascii=False)
                    sql = text(
                        """
                        INSERT OR IGNORE INTO market_events
                            (event_type, symbol, event_date, title, source, metadata)
                        VALUES (:etype, :symbol, :edate, :title, :source, :metadata)
                        """
                    )
                else:
                    metadata_payload = event.metadata
                    sql = text(
                        """
                        INSERT INTO market_events
                            (event_type, symbol, event_date, title, source, metadata)
                        VALUES (:etype, :symbol, :edate, :title, :source, :metadata)
                        ON CONFLICT (symbol, event_date, event_type) DO NOTHING
                        RETURNING id
                        """
                    )
                params = {
                    "etype": event.event_type.value,
                    "symbol": event.symbol,
                    "edate": event.event_date.isoformat() if is_sqlite else event.event_date,
                    "title": event.title,
                    "source": event.source,
                    "metadata": metadata_payload,
                }
                res = await db.execute(sql, params)
                if (res.rowcount or 0) != 1:
                    return None
                if is_sqlite:
                    res2 = await db.execute(text("SELECT last_insert_rowid() AS id"))
                    row = res2.mappings().first()
                    return int(row["id"]) if row else None
                row = res.mappings().first()
                return int(row["id"]) if row else None
        except Exception as exc:
            logger.error("event insert 실패: %s", exc)
            return None

    async def find_by_date_range(
        self, start: date, end: date, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        try:
            async with get_db() as db:
                if db is None:
                    return []
                is_sqlite = db.engine.dialect.name == "sqlite"
                params = {
                    "start": start.isoformat() if is_sqlite else start,
                    "end": end.isoformat() if is_sqlite else end,
                    "limit": limit,
                }
                res = await db.execute(
                    text(
                        "SELECT * FROM market_events "
                        "WHERE event_date BETWEEN :start AND :end "
                        "ORDER BY event_date ASC LIMIT :limit"
                    ),
                    params,
                )
                return [dict(row) for row in res.mappings().all()]
        except Exception as exc:
            logger.error("find_by_date_range 실패: %s", exc)
            return []

    async def find_by_symbol(
        self, symbol: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        try:
            async with get_db() as db:
                if db is None:
                    return []
                res = await db.execute(
                    text(
                        "SELECT * FROM market_events WHERE symbol = :symbol "
                        "ORDER BY event_date DESC LIMIT :limit"
                    ),
                    {"symbol": symbol, "limit": limit},
                )
                return [dict(row) for row in res.mappings().all()]
        except Exception as exc:
            logger.error("find_by_symbol 실패: %s", exc)
            return []


event_repo = EventRepository()


__all__ = ["EventRepository", "event_repo"]
