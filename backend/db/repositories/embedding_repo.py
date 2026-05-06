"""
BAR-58 — EmbeddingRepository.

text() + named param + dialect 분기 (BAR-56/57 패턴 답습).
- Postgres: vector(768) bind (BAR-58b 운영 시 pgvector adapter register)
- SQLite fallback: TEXT 컬럼 (json round-trip)
- search_similar: cosine **distance** ASC (낮을수록 유사) — council 합의
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Tuple

import numpy as np
from sqlalchemy import text

from backend.db.database import get_db
from backend.models.embedding import EmbeddingResult

logger = logging.getLogger(__name__)


class EmbeddingRepository:
    """임베딩 적재 + 유사 검색."""

    expected_dim: int = 768

    async def insert(self, result: EmbeddingResult) -> bool:
        """ON CONFLICT DO NOTHING. inserted == 1 이면 True."""
        try:
            async with get_db() as db:
                if db is None:
                    return False
                is_pg = db.engine.dialect.name == "postgresql"
                vec_list = [float(x) for x in result.vector]

                if is_pg:
                    sql = text(
                        """
                        INSERT INTO embeddings
                            (news_id, model, vector, created_at)
                        VALUES (:news_id, :model, :vector, NOW())
                        ON CONFLICT (news_id, model) DO NOTHING
                        """
                    )
                    params = {
                        "news_id": result.news_db_id,
                        "model": result.model,
                        "vector": vec_list,  # BAR-58b 에서 pgvector adapter 가 변환
                    }
                else:
                    sql = text(
                        """
                        INSERT OR IGNORE INTO embeddings
                            (news_id, model, vector, created_at)
                        VALUES (:news_id, :model, :vector, :now)
                        """
                    )
                    params = {
                        "news_id": result.news_db_id,
                        "model": result.model,
                        "vector": json.dumps(vec_list),
                        "now": datetime.now(timezone.utc).isoformat(),
                    }
                res = await db.execute(sql, params)
                return (res.rowcount or 0) == 1
        except Exception as exc:
            logger.error("embedding insert 실패: %s", type(exc).__name__)
            return False

    async def search_similar(
        self,
        query_vec: tuple[float, ...],
        model: str,
        top_k: int = 10,
    ) -> List[Tuple[int, float]]:
        """반환: list[(news_id, distance)] — cosine distance ASC.

        distance = 1 - cosine_similarity (Postgres `<=>` operator 와 일치).
        """
        try:
            async with get_db() as db:
                if db is None:
                    return []
                is_pg = db.engine.dialect.name == "postgresql"
                if is_pg:
                    sql = text(
                        """
                        SELECT news_id, (vector <=> :q) AS distance
                        FROM embeddings
                        WHERE model = :model
                        ORDER BY vector <=> :q ASC
                        LIMIT :k
                        """
                    )
                    res = await db.execute(
                        sql,
                        {"q": list(query_vec), "model": model, "k": top_k},
                    )
                    return [
                        (int(row["news_id"]), float(row["distance"]))
                        for row in res.mappings().all()
                    ]
                # SQLite fallback — Python 측 cosine distance
                res = await db.execute(
                    text(
                        "SELECT news_id, vector FROM embeddings WHERE model = :model"
                    ),
                    {"model": model},
                )
                q = np.asarray(query_vec, dtype=np.float32)
                qn = float(np.linalg.norm(q)) or 1.0
                pairs: list[tuple[int, float]] = []
                for r in res.mappings().all():
                    v = np.asarray(json.loads(r["vector"]), dtype=np.float32)
                    vn = float(np.linalg.norm(v)) or 1.0
                    sim = float(np.dot(q, v) / (qn * vn))
                    pairs.append((int(r["news_id"]), 1.0 - sim))
                pairs.sort(key=lambda kv: kv[1])
                return pairs[:top_k]
        except Exception as exc:
            logger.error("embedding search_similar 실패: %s", type(exc).__name__)
            return []


embedding_repo = EmbeddingRepository()


__all__ = ["EmbeddingRepository", "embedding_repo"]
