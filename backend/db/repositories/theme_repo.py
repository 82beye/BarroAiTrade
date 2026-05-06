"""
BAR-59 — ThemeRepository.

themes / theme_keywords / theme_stocks CRUD.
text() + named param + dialect 분기 (BAR-56/57/58 패턴 답습).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.db.database import get_db

logger = logging.getLogger(__name__)


class ThemeRepository:
    """테마·키워드·종목 매핑 CRUD."""

    async def upsert_theme(self, name: str, description: str = "") -> Optional[int]:
        """theme 조회·삽입. 신규 시 BIGSERIAL id 반환."""
        try:
            async with get_db() as db:
                if db is None:
                    return None
                # 이미 존재하면 id 반환
                res = await db.execute(
                    text("SELECT id FROM themes WHERE name = :name"),
                    {"name": name},
                )
                row = res.mappings().first()
                if row is not None:
                    return int(row["id"])

                is_sqlite = db.engine.dialect.name == "sqlite"
                if is_sqlite:
                    await db.execute(
                        text(
                            "INSERT INTO themes (name, description, created_at) "
                            "VALUES (:name, :desc, :now)"
                        ),
                        {
                            "name": name,
                            "desc": description,
                            "now": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    res2 = await db.execute(
                        text("SELECT last_insert_rowid() AS id")
                    )
                else:
                    res2 = await db.execute(
                        text(
                            "INSERT INTO themes (name, description) "
                            "VALUES (:name, :desc) RETURNING id"
                        ),
                        {"name": name, "desc": description},
                    )
                row2 = res2.mappings().first()
                return int(row2["id"]) if row2 else None
        except Exception as exc:
            logger.error("upsert_theme 실패: %s", exc)
            return None

    async def add_keyword(self, theme_id: int, keyword: str) -> bool:
        try:
            async with get_db() as db:
                if db is None:
                    return False
                is_sqlite = db.engine.dialect.name == "sqlite"
                sql = text(
                    "INSERT OR IGNORE INTO theme_keywords (theme_id, keyword) "
                    "VALUES (:tid, :kw)"
                ) if is_sqlite else text(
                    "INSERT INTO theme_keywords (theme_id, keyword) "
                    "VALUES (:tid, :kw) ON CONFLICT (theme_id, keyword) DO NOTHING"
                )
                res = await db.execute(sql, {"tid": theme_id, "kw": keyword})
                return (res.rowcount or 0) == 1
        except Exception as exc:
            logger.error("add_keyword 실패: %s", exc)
            return False

    async def link_stock(
        self, theme_id: int, symbol: str, score: float
    ) -> bool:
        try:
            async with get_db() as db:
                if db is None:
                    return False
                is_sqlite = db.engine.dialect.name == "sqlite"
                if is_sqlite:
                    sql = text(
                        "INSERT OR REPLACE INTO theme_stocks "
                        "(theme_id, symbol, score) VALUES (:tid, :sym, :score)"
                    )
                else:
                    sql = text(
                        "INSERT INTO theme_stocks (theme_id, symbol, score) "
                        "VALUES (:tid, :sym, :score) "
                        "ON CONFLICT (theme_id, symbol) "
                        "DO UPDATE SET score = EXCLUDED.score"
                    )
                res = await db.execute(
                    sql, {"tid": theme_id, "sym": symbol, "score": float(score)}
                )
                return (res.rowcount or 0) >= 1
        except Exception as exc:
            logger.error("link_stock 실패: %s", exc)
            return False

    async def find_themes_by_stock(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            async with get_db() as db:
                if db is None:
                    return []
                res = await db.execute(
                    text(
                        "SELECT t.id, t.name, ts.score "
                        "FROM themes t JOIN theme_stocks ts ON ts.theme_id = t.id "
                        "WHERE ts.symbol = :symbol "
                        "ORDER BY ts.score DESC"
                    ),
                    {"symbol": symbol},
                )
                return [dict(row) for row in res.mappings().all()]
        except Exception as exc:
            logger.error("find_themes_by_stock 실패: %s", exc)
            return []


theme_repo = ThemeRepository()


__all__ = ["ThemeRepository", "theme_repo"]
