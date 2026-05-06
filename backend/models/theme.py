"""
BAR-59 — Theme classification models (Pydantic v2 frozen).

tags 는 frozen 호환을 위해 tuple. attempted 는 fallback 추적.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ClassificationResult(BaseModel):
    """분류 결과. frozen — 외부 변조 차단."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: tuple[str, ...] = ()                  # 정렬·중복 제거 (NewsItem 패턴 답습)
    scores: dict[str, float] = Field(default_factory=dict)
    backend: str = ""                            # "tfidf_lr_v1" / "three_tier_v1:fallback_no_tier3:from_..."
    confidence: float = 0.0
    attempted: tuple[str, ...] = ()              # tier1→tier2→tier3 누적


__all__ = ["ClassificationResult"]
