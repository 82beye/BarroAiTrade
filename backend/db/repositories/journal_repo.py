"""BAR-65 — JournalRepository (text() + dialect 분기)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.db.database import get_db
from backend.models.journal import Emotion, TradeNote

logger = logging.getLogger(__name__)


class JournalRepository:
    async def insert(self, note: TradeNote) -> Optional[int]:
        try:
            async with get_db() as db:
                if db is None:
                    return None
                is_sqlite = db.engine.dialect.name == "sqlite"
                tags_payload = (
                    json.dumps(list(note.tags), ensure_ascii=False)
                    if is_sqlite
                    else list(note.tags)
                )
                if is_sqlite:
                    sql = text(
                        """
                        INSERT OR IGNORE INTO trade_notes
                            (trade_id, symbol, side, qty, entry_price,
                             exit_price, pnl, entry_time, exit_time,
                             emotion, note, tags)
                        VALUES (:tid, :sym, :side, :qty, :ep, :xp, :pnl,
                                :et, :xt, :emo, :note, :tags)
                        """
                    )
                else:
                    sql = text(
                        """
                        INSERT INTO trade_notes
                            (trade_id, symbol, side, qty, entry_price,
                             exit_price, pnl, entry_time, exit_time,
                             emotion, note, tags)
                        VALUES (:tid, :sym, :side, :qty, :ep, :xp, :pnl,
                                :et, :xt, :emo, :note, :tags)
                        ON CONFLICT (trade_id) DO NOTHING
                        RETURNING id
                        """
                    )
                params = {
                    "tid": note.trade_id,
                    "sym": note.symbol,
                    "side": note.side,
                    "qty": float(note.qty),
                    "ep": float(note.entry_price),
                    "xp": float(note.exit_price) if note.exit_price else None,
                    "pnl": float(note.pnl) if note.pnl else None,
                    "et": note.entry_time.isoformat() if is_sqlite else note.entry_time,
                    "xt": (
                        note.exit_time.isoformat() if note.exit_time and is_sqlite
                        else note.exit_time
                    ),
                    "emo": note.emotion.value,
                    "note": note.note,
                    "tags": tags_payload,
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
            logger.error("journal insert 실패: %s", exc)
            return None

    async def find_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            async with get_db() as db:
                if db is None:
                    return []
                res = await db.execute(
                    text(
                        "SELECT * FROM trade_notes WHERE symbol = :sym "
                        "ORDER BY entry_time DESC LIMIT :limit"
                    ),
                    {"sym": symbol, "limit": limit},
                )
                return [dict(row) for row in res.mappings().all()]
        except Exception as exc:
            logger.error("find_by_symbol 실패: %s", exc)
            return []

    async def update_emotion(self, note_id: int, emotion: Emotion) -> bool:
        try:
            async with get_db() as db:
                if db is None:
                    return False
                res = await db.execute(
                    text("UPDATE trade_notes SET emotion = :emo WHERE id = :id"),
                    {"emo": emotion.value, "id": note_id},
                )
                return (res.rowcount or 0) == 1
        except Exception as exc:
            logger.error("update_emotion 실패: %s", exc)
            return False


journal_repo = JournalRepository()


__all__ = ["JournalRepository", "journal_repo"]
