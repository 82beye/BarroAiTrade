"""BAR-58 — EmbeddingRepository (4 cases). SQLite fallback 위에서 검증."""
from __future__ import annotations

import os

import numpy as np
import pytest
from sqlalchemy import text

from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.embedding_repo import EmbeddingRepository
from backend.models.embedding import EmbeddingResult


@pytest.fixture
async def isolated_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "emb_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    # SQLite 에 embeddings 테이블 직접 생성 (alembic 0003 SQLite 분기 동등)
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_id INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(news_id, model)
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


def _vec(dim=768, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.random(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return tuple(float(x) for x in v)


class TestEmbeddingRepo:
    @pytest.mark.asyncio
    async def test_insert_new_returns_true(self, isolated_db):
        repo = EmbeddingRepository()
        result = EmbeddingResult(
            news_db_id=1, model="fake-deterministic-768", vector=_vec(seed=1)
        )
        ok = await repo.insert(result)
        assert ok is True

    @pytest.mark.asyncio
    async def test_insert_duplicate_returns_false(self, isolated_db):
        repo = EmbeddingRepository()
        v = _vec(seed=2)
        await repo.insert(EmbeddingResult(news_db_id=2, model="m1", vector=v))
        ok = await repo.insert(
            EmbeddingResult(news_db_id=2, model="m1", vector=v)
        )
        assert ok is False  # ON CONFLICT (news_id, model) DO NOTHING

    @pytest.mark.asyncio
    async def test_search_similar_sqlite_cosine(self, isolated_db):
        repo = EmbeddingRepository()
        v_a = _vec(seed=10)
        v_b = _vec(seed=20)
        await repo.insert(EmbeddingResult(news_db_id=10, model="m", vector=v_a))
        await repo.insert(EmbeddingResult(news_db_id=20, model="m", vector=v_b))
        # query == v_a → news_id 10 이 distance ≈ 0
        results = await repo.search_similar(v_a, model="m", top_k=2)
        assert len(results) == 2
        # ASC: 가장 유사한 건 v_a 자체
        assert results[0][0] == 10
        assert results[0][1] < results[1][1]

    @pytest.mark.asyncio
    async def test_text_round_trip_precision(self, isolated_db):
        """SQLite TEXT 직렬화 후 round-trip 정밀도 (norm ≈ 1.0 ± 1e-5)."""
        repo = EmbeddingRepository()
        v = _vec(seed=42)
        await repo.insert(EmbeddingResult(news_db_id=99, model="m", vector=v))
        # search_similar 가 vector 다시 읽어 cosine 계산 → 자기 자신과의 distance ≈ 0
        results = await repo.search_similar(v, model="m", top_k=1)
        assert len(results) == 1
        assert results[0][0] == 99
        assert abs(results[0][1]) < 1e-5
