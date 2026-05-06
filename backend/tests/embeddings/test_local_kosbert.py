"""BAR-58 — LocalKoSbertEmbedder (3 cases). 실 모델 다운로드 X — lazy import 검증만."""
from __future__ import annotations

import pytest

from backend.core.embeddings.embedder import LocalKoSbertEmbedder


class TestLocalKoSbert:
    def test_revision_required_raises(self):
        with pytest.raises(ValueError, match="revision must be pinned"):
            LocalKoSbertEmbedder(revision="")

    def test_constructs_with_revision_pin(self):
        e = LocalKoSbertEmbedder(revision="abc123def")
        assert e.dim == 768
        assert e._model is None  # lazy

    def test_dim_attribute_immutable_via_init(self):
        e = LocalKoSbertEmbedder(revision="x", expected_dim=768)
        assert e.dim == 768
        # name 은 클래스 attribute
        assert e.name == "ko-sbert-768"
