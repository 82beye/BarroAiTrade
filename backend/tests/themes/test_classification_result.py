"""BAR-59 — ClassificationResult 모델 (2 cases)."""
from __future__ import annotations

import pytest

from backend.models.theme import ClassificationResult


def test_frozen_and_tuple_tags():
    r = ClassificationResult(
        tags=("AI", "반도체"),
        scores={"AI": 0.9, "반도체": 0.8},
        backend="tfidf_lr_v1",
        confidence=0.9,
    )
    assert isinstance(r.tags, tuple)
    with pytest.raises(Exception):
        r.tags = ("modified",)  # type: ignore[misc]


def test_attempted_default_and_preserved():
    r = ClassificationResult()
    assert r.attempted == ()
    r2 = r.model_copy(update={"attempted": ("a", "b")})
    assert r2.attempted == ("a", "b")
