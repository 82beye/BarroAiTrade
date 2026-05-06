"""
BAR-59 — 3-tier 테마 분류기.

- TfidfLogRegClassifier: sklearn TF-IDF + LR + kiwipiepy NN/VV/VA (1차)
- EmbeddingCosineClassifier: prototype 5종 in-memory cosine (2차)
- ClaudeHaikuClassifier: lazy stub — classify() 진입 시 NotImplementedError (3차)
- ThreeTierClassifier: orchestrator (1→2→3 + best-effort fallback)
- ClassifierFactory: settings 기반 + auto-fit
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, ClassVar, Optional, Protocol, runtime_checkable

import numpy as np
from pydantic import SecretStr

from backend.models.news import NewsItem
from backend.models.theme import ClassificationResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────


@runtime_checkable
class ThemeClassifier(Protocol):
    backend_id: ClassVar[str]

    async def classify(self, news_item: NewsItem) -> ClassificationResult: ...
    async def _redact(self, text: str) -> str: ...   # security CWE-200 hook


# ─────────────────────────────────────────────
# 1차 — TfidfLogRegClassifier
# ─────────────────────────────────────────────


# module-level singleton (developer 권고 — joblib pickle 호환 + lazy)
_KIWI: Optional[Any] = None


def _kiwi_tokenize(text: str) -> list[str]:
    """한국어 형태소 추출 — NN (명사) / VV (동사) / VA (형용사)만."""
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi

        _KIWI = Kiwi()
    return [
        token.form
        for token in _KIWI.tokenize(text)
        if token.tag.startswith(("NN", "VV", "VA"))
    ]


class TfidfLogRegClassifier:
    """sklearn TF-IDF + LogisticRegression. solver='liblinear' (결정성)."""

    backend_id: ClassVar[str] = "tfidf_lr_v1"

    def __init__(self, threshold: float = 0.7) -> None:
        self._pipeline: Optional[Any] = None
        self._threshold = threshold

    def fit(self, samples: list[tuple[str, str]]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.multiclass import OneVsRestClassifier
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import LabelBinarizer

        clf = OneVsRestClassifier(
            LogisticRegression(solver="liblinear", random_state=42)
        )
        self._pipeline = Pipeline(
            [
                (
                    "tfidf",
                    TfidfVectorizer(
                        tokenizer=_kiwi_tokenize,
                        ngram_range=(1, 2),
                        max_features=5000,
                        lowercase=False,
                        token_pattern=None,  # tokenizer 사용 시 명시
                    ),
                ),
                ("lr", clf),
            ]
        )
        texts = [t for t, _ in samples]
        themes = [theme for _, theme in samples]
        # OneVsRestClassifier 는 multilabel 입력 — 단일 라벨이라도 [[label]] 형태
        lb = LabelBinarizer()
        y = lb.fit_transform(themes)
        self._pipeline.fit(texts, y)
        self._labels = list(lb.classes_)

    async def classify(self, news_item: NewsItem) -> ClassificationResult:
        if self._pipeline is None:
            return ClassificationResult(
                backend=self.backend_id, attempted=(self.backend_id,)
            )
        text = await self._redact(f"{news_item.title} {news_item.body}")
        try:
            proba = self._pipeline.predict_proba([text])[0]
        except Exception as exc:
            logger.warning("tfidf_lr predict failed: %s", exc)
            return ClassificationResult(
                backend=self.backend_id, attempted=(self.backend_id,)
            )
        scores = {label: float(p) for label, p in zip(self._labels, proba)}
        tags = tuple(
            sorted({c for c, p in scores.items() if p >= self._threshold})
        )
        confidence = max(scores.values()) if scores else 0.0
        return ClassificationResult(
            tags=tags,
            scores=scores,
            backend=self.backend_id,
            confidence=confidence,
            attempted=(self.backend_id,),
        )

    async def _redact(self, text: str) -> str:
        return text  # BAR-59a no-op


# ─────────────────────────────────────────────
# 2차 — EmbeddingCosineClassifier
# ─────────────────────────────────────────────


class EmbeddingCosineClassifier:
    """prototype 5종 in-memory cosine. Embedder L2 normalize 가정."""

    backend_id: ClassVar[str] = "embedding_cosine_v1"

    def __init__(
        self,
        embedder,
        theme_prototypes: dict[str, str],
        threshold: float = 0.5,
    ) -> None:
        self._embedder = embedder
        self._theme_prototypes = theme_prototypes
        self._threshold = threshold
        self._proto_vecs: Optional[dict[str, np.ndarray]] = None
        self._dim: Optional[int] = None

    async def _ensure_prototypes(self) -> None:
        if self._proto_vecs is not None:
            return
        themes = list(self._theme_prototypes.keys())
        texts = list(self._theme_prototypes.values())
        vecs = await self._embedder.encode(texts)
        self._proto_vecs = dict(zip(themes, vecs))
        if vecs:
            self._dim = vecs[0].shape[0]

    async def classify(self, news_item: NewsItem) -> ClassificationResult:
        await self._ensure_prototypes()
        text = await self._redact(f"{news_item.title} {news_item.body}")
        vecs = await self._embedder.encode([text])
        if not vecs:
            return ClassificationResult(
                backend=self.backend_id, attempted=(self.backend_id,)
            )
        news_vec = vecs[0]
        if self._dim is not None and news_vec.shape[0] != self._dim:
            raise ValueError(
                f"dim mismatch: news={news_vec.shape[0]} prototype={self._dim}"
            )
        scores: dict[str, float] = {}
        for theme, pv in (self._proto_vecs or {}).items():
            sim = float(np.dot(news_vec, pv))
            scores[theme] = 1.0 - sim
        tags = tuple(
            sorted({c for c, d in scores.items() if d <= self._threshold})
        )
        confidence = 1.0 - (min(scores.values()) if scores else 1.0)
        return ClassificationResult(
            tags=tags,
            scores=scores,
            backend=self.backend_id,
            confidence=confidence,
            attempted=(self.backend_id,),
        )

    async def _redact(self, text: str) -> str:
        return text


# ─────────────────────────────────────────────
# 3차 — ClaudeHaikuClassifier (lazy stub)
# ─────────────────────────────────────────────


class ClaudeHaikuClassifier:
    """council (architect/developer/reviewer) 합의: __init__ 정상 + classify() raise."""

    backend_id: ClassVar[str] = "claude_haiku_v1"

    def __init__(self, api_key: Optional[SecretStr] = None) -> None:
        self._api_key = api_key  # BAR-59b 활성화

    async def classify(self, news_item: NewsItem) -> ClassificationResult:
        raise NotImplementedError("ClaudeHaikuClassifier — BAR-59b 활성화 예정")

    async def _redact(self, text: str) -> str:
        return text  # BAR-59b 정규식 + presidio


# ─────────────────────────────────────────────
# Orchestrator — ThreeTierClassifier
# ─────────────────────────────────────────────


class ThreeTierClassifier:
    """1차 high → 2차 mid → 3차 fallback (NotImplementedError catch)."""

    backend_id: ClassVar[str] = "three_tier_v1"

    def __init__(self, tier1, tier2, tier3) -> None:
        self._tier1 = tier1
        self._tier2 = tier2
        self._tier3 = tier3

    async def classify(self, news_item: NewsItem) -> ClassificationResult:
        attempted: list[str] = []

        # tier1 — TF-IDF + LR
        r1 = await self._tier1.classify(news_item)
        attempted.append(self._tier1.backend_id)
        if r1.tags and r1.confidence >= 0.7:
            return r1.model_copy(update={"attempted": tuple(attempted)})

        # tier2 — Embedding cosine
        r2 = await self._tier2.classify(news_item)
        attempted.append(self._tier2.backend_id)
        if r2.tags and r2.confidence >= 0.5:
            return r2.model_copy(update={"attempted": tuple(attempted)})

        # tier3 — Claude haiku (stub: NotImplementedError catch)
        try:
            r3 = await self._tier3.classify(news_item)
            attempted.append(self._tier3.backend_id)
            return r3.model_copy(update={"attempted": tuple(attempted)})
        except NotImplementedError:
            attempted.append(self._tier3.backend_id)
            best = r1 if r1.confidence >= r2.confidence else r2
            from_marker = (
                f"{self.backend_id}:fallback_no_tier3:from_{best.backend}"
            )
            return best.model_copy(
                update={
                    "backend": from_marker,
                    "attempted": tuple(attempted),
                }
            )

    async def _redact(self, text: str) -> str:
        return text


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────


class ClassifierFactory:
    """settings 기반 + 자동 fit."""

    @staticmethod
    def from_settings(settings, embedder=None) -> ThemeClassifier:
        backend = settings.news_theme_backend
        labels_path = getattr(settings, "news_theme_labels_path", None)

        if backend == "tfidf":
            cls = TfidfLogRegClassifier(
                threshold=settings.news_theme_threshold_tfidf
            )
            samples = ClassifierFactory._load_samples(labels_path)
            cls.fit(samples)
            return cls
        if backend == "cosine":
            if embedder is None:
                raise ValueError("embedder required for cosine backend")
            return EmbeddingCosineClassifier(
                embedder=embedder,
                theme_prototypes=ClassifierFactory._load_prototypes(labels_path),
                threshold=settings.news_theme_threshold_cosine,
            )
        if backend == "haiku":
            return ClaudeHaikuClassifier(
                api_key=getattr(settings, "anthropic_api_key", None)
            )
        if backend == "three_tier":
            tier1 = TfidfLogRegClassifier(
                threshold=settings.news_theme_threshold_tfidf
            )
            samples = ClassifierFactory._load_samples(labels_path)
            tier1.fit(samples)
            if embedder is None:
                raise ValueError("embedder required for three_tier backend")
            tier2 = EmbeddingCosineClassifier(
                embedder=embedder,
                theme_prototypes=ClassifierFactory._load_prototypes(labels_path),
                threshold=settings.news_theme_threshold_cosine,
            )
            tier3 = ClaudeHaikuClassifier(
                api_key=getattr(settings, "anthropic_api_key", None)
            )
            return ThreeTierClassifier(tier1, tier2, tier3)
        raise ValueError(f"unknown theme backend: {backend}")

    @staticmethod
    def _load_samples(path: Optional[str]) -> list[tuple[str, str]]:
        if not path or not os.path.exists(path):
            raise ValueError(f"news_theme_labels_path missing: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            (text, theme) for theme, texts in data.items() for text in texts
        ]

    @staticmethod
    def _load_prototypes(path: Optional[str]) -> dict[str, str]:
        if not path or not os.path.exists(path):
            raise ValueError(f"news_theme_labels_path missing: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {theme: texts[0] for theme, texts in data.items()}


__all__ = [
    "ThemeClassifier",
    "TfidfLogRegClassifier",
    "EmbeddingCosineClassifier",
    "ClaudeHaikuClassifier",
    "ThreeTierClassifier",
    "ClassifierFactory",
]
