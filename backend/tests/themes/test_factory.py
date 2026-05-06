"""BAR-59 — ClassifierFactory (4 cases)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.embeddings.embedder import FakeDeterministicEmbedder
from backend.core.themes.classifier import (
    ClaudeHaikuClassifier,
    ClassifierFactory,
    EmbeddingCosineClassifier,
    TfidfLogRegClassifier,
    ThreeTierClassifier,
)


FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "theme_labels.json"


def _settings(backend="three_tier", labels_path=str(FIXTURE)):
    return SimpleNamespace(
        news_theme_backend=backend,
        news_theme_threshold_tfidf=0.7,
        news_theme_threshold_cosine=0.5,
        news_theme_labels_path=labels_path,
        anthropic_api_key=None,
    )


class TestFactory:
    def test_tfidf_backend_auto_fits(self):
        cls = ClassifierFactory.from_settings(_settings("tfidf"))
        assert isinstance(cls, TfidfLogRegClassifier)
        # auto-fit 이 일어나야 함 — pipeline 존재
        assert cls._pipeline is not None

    def test_cosine_backend_requires_embedder(self):
        with pytest.raises(ValueError, match="embedder required"):
            ClassifierFactory.from_settings(_settings("cosine"))

    def test_haiku_backend_constructs_stub(self):
        cls = ClassifierFactory.from_settings(_settings("haiku"))
        assert isinstance(cls, ClaudeHaikuClassifier)

    def test_three_tier_backend_with_embedder(self):
        embedder = FakeDeterministicEmbedder()
        cls = ClassifierFactory.from_settings(
            _settings("three_tier"), embedder=embedder
        )
        assert isinstance(cls, ThreeTierClassifier)
        assert isinstance(cls._tier1, TfidfLogRegClassifier)
        assert isinstance(cls._tier2, EmbeddingCosineClassifier)
        assert isinstance(cls._tier3, ClaudeHaikuClassifier)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown theme backend"):
            ClassifierFactory.from_settings(_settings("invalid"))

    def test_labels_path_missing_raises(self):
        with pytest.raises(ValueError, match="labels_path missing"):
            ClassifierFactory.from_settings(_settings("tfidf", labels_path=None))
