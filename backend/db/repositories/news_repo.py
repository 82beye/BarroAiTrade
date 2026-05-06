"""
BAR-57 — NewsRepository.

audit_repo 패턴 답습 — text() + named param + dialect 분기.
ON CONFLICT DO NOTHING (Postgres) / INSERT OR IGNORE (SQLite). 결과 1 row 일 때만 True.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from sqlalchemy import text

from backend.db.database import get_db
from backend.models.news import NewsItem, NewsSource

logger = logging.getLogger(__name__)


class NewsRepository:
    """뉴스 항목 CRUD."""

    async def insert(self, item: NewsItem) -> bool:
        """ON CONFLICT DO NOTHING. inserted == 1 이면 True (publish 트리거)."""
        try:
            async with get_db() as db:
                if db is None:
                    return False

                if db.engine.dialect.name == "sqlite":
                    tags_payload: Any = json.dumps(list(item.tags), ensure_ascii=False)
                    sql = text(
                        """
                        INSERT OR IGNORE INTO news_items
                            (source, source_id, title, body, url,
                             published_at, fetched_at, tags)
                        VALUES (:source, :source_id, :title, :body, :url,
                                :published_at, :fetched_at, :tags)
                        """
                    )
                else:
                    tags_payload = list(item.tags)
                    sql = text(
                        """
                        INSERT INTO news_items
                            (source, source_id, title, body, url,
                             published_at, fetched_at, tags)
                        VALUES (:source, :source_id, :title, :body, :url,
                                :published_at, :fetched_at, :tags)
                        ON CONFLICT (source, source_id) DO NOTHING
                        """
                    )

                result = await db.execute(
                    sql,
                    {
                        "source": item.source.value,
                        "source_id": item.source_id,
                        "title": item.title,
                        "body": item.body,
                        "url": item.url,
                        # SQLite TEXT 컬럼이므로 ISO 8601 직렬화
                        "published_at": (
                            item.published_at.isoformat()
                            if db.engine.dialect.name == "sqlite"
                            else item.published_at
                        ),
                        "fetched_at": (
                            item.fetched_at.isoformat()
                            if db.engine.dialect.name == "sqlite"
                            else item.fetched_at
                        ),
                        "tags": tags_payload,
                    },
                )
                return (result.rowcount or 0) == 1
        except Exception as exc:
            logger.error("news insert 실패: %s", exc)
            return False

    async def find_recent_by_source(
        self,
        source: NewsSource,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        try:
            async with get_db() as db:
                if db is None:
                    return []
                sql = text(
                    "SELECT * FROM news_items WHERE source = :source "
                    "ORDER BY published_at DESC LIMIT :limit"
                )
                result = await db.execute(
                    sql, {"source": source.value, "limit": limit}
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception as exc:
            logger.error("news find_recent_by_source 실패: %s", exc)
            return []


news_repo = NewsRepository()


__all__ = ["NewsRepository", "news_repo"]
