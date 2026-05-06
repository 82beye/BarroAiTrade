"""BAR-58 — create_embedder 팩토리 (3 cases)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.embeddings.embedder import (
    FakeDeterministicEmbedder,
    LocalKoSbertEmbedder,
    create_embedder,
)


def _settings(backend, **kwargs):
    base = {
        "news_embedding_backend": backend,
        "news_embedding_model": "jhgan/ko-sroberta-multitask",
        "news_embedding_dim": 768,
        "news_embedding_revision": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestFactory:
    def test_fake_backend(self):
        e = create_embedder(_settings("fake"))
        assert isinstance(e, FakeDeterministicEmbedder)

    def test_ko_sbert_backend(self):
        e = create_embedder(
            _settings("ko_sbert", news_embedding_revision="abc123def")
        )
        assert isinstance(e, LocalKoSbertEmbedder)

    def test_openai_backend_not_implemented(self):
        with pytest.raises(NotImplementedError, match="BAR-58b"):
            create_embedder(_settings("openai"))
