"""
BAR-58 — Embedding job & result models (Pydantic v2 frozen).

자금흐름 X. body 트렁케이션 (CWE-1284 DoS 방지) — MAX_EMBED_CHARS=8192.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# security 권고 (CWE-1284) — body 길이 상한
MAX_EMBED_CHARS: int = 8192


class EmbeddingJob(BaseModel):
    """Worker 가 stream 에서 가져온 단위 작업."""

    model_config = ConfigDict(frozen=True)

    news_db_id: int = Field(gt=0)
    body: str = Field(max_length=MAX_EMBED_CHARS)
    stream_id: str = Field(min_length=1)


class EmbeddingResult(BaseModel):
    """Repo 적재 직전 단위. vector 는 tuple[float] (frozen 호환)."""

    model_config = ConfigDict(frozen=True)

    news_db_id: int = Field(gt=0)
    model: str = Field(min_length=1, max_length=128)
    vector: tuple[float, ...]
    created_at: Optional[datetime] = None


__all__ = ["MAX_EMBED_CHARS", "EmbeddingJob", "EmbeddingResult"]
